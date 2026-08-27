from __future__ import annotations

from hy3_api_review_evaluator.redaction import REDACTED, redact_structure, redact_text


def test_redacts_credentials_and_exact_secret() -> None:
    secret = "a-local-provider-secret"
    text = f"Authorization: Bearer abcdefghijklmnop; custom={secret}"
    result = redact_text(text, exact_secrets=[secret])
    assert "abcdefghijklmnop" not in result
    assert secret not in result
    assert REDACTED in result


def test_structural_redaction_preserves_schema_named_api_key() -> None:
    value = {"api_key": "real-value", "properties": {"api_key": {"type": "string"}}}
    redacted = redact_structure(value)
    assert redacted["api_key"] == REDACTED
    assert redacted["properties"]["api_key"]["type"] == "string"
