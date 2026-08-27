from __future__ import annotations

import json

import pytest

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import SpecInputError
from hy3_api_review_evaluator.spec_loader import (
    compact_for_model,
    load_spec_bytes,
    load_spec_text,
    resolve_local_object,
)

VALID = """
openapi: 3.1.0
info: {title: Demo, version: 1.0.0}
paths:
  /health:
    get:
      responses:
        '200': {description: ok}
"""


def test_loads_openapi_and_computes_metadata(settings: Settings) -> None:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    assert spec.title == "Demo"
    assert spec.operation_count == 1
    assert len(spec.sha256) == 64


def test_rejects_oversized_and_non_utf8_input(settings: Settings) -> None:
    with pytest.raises(SpecInputError, match="exceeds"):
        load_spec_bytes(b"x" * (settings.max_file_bytes + 1), "demo.yaml", settings)
    with pytest.raises(SpecInputError, match="UTF-8"):
        load_spec_bytes(b"\xff\xfe\xfd", "demo.yaml", settings)


def test_rejects_yaml_aliases(settings: Settings) -> None:
    malicious = """
openapi: 3.1.0
info: {title: Demo, version: 1.0.0}
paths: &paths
  /health: {get: {responses: {'200': {description: ok}}}}
x-copy: *paths
"""
    with pytest.raises(SpecInputError, match="aliases"):
        load_spec_text(malicious, "demo.yaml", settings)


def test_records_but_never_resolves_external_refs(settings: Settings) -> None:
    spec = load_spec_text(
        VALID + "\ncomponents:\n  schemas:\n    User: {$ref: 'https://example.test/user.yaml'}\n",
        "demo.yaml",
        settings,
    )
    assert spec.external_refs == ("https://example.test/user.yaml",)
    assert resolve_local_object(spec.document, {"$ref": spec.external_refs[0]}) is None


def test_resolves_local_ref(settings: Settings) -> None:
    spec = load_spec_text(
        VALID + "\ncomponents:\n"
        "  schemas:\n"
        "    User:\n"
        "      type: object\n"
        "      properties: {id: {type: string}}\n",
        "demo.yaml",
        settings,
    )
    resolved = resolve_local_object(spec.document, {"$ref": "#/components/schemas/User"})
    assert resolved and resolved["type"] == "object"


def test_model_projection_is_redacted_bounded_and_valid_json(settings: Settings) -> None:
    secret = "abcdefghijklmnop"
    spec = load_spec_text(
        VALID.replace(
            "description: ok",
            f"description: 'Bearer {secret} " + ("bounded context " * 100) + "'",
        ),
        "demo.yaml",
        settings,
    )
    projection = compact_for_model(spec, 300)
    assert secret not in projection
    assert len(projection) <= 300
    assert json.loads(projection)["truncated"] is True
