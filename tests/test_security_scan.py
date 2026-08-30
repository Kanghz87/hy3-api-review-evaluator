from __future__ import annotations

from hy3_api_review_evaluator.security_scan import scan_text


def test_scanner_reports_rule_and_location_without_secret_value() -> None:
    value = "real-looking-value-1234567890"
    assignment_name = "HY3_API_KEY"
    findings = scan_text(f"{assignment_name}={value}\n", path="bad.env")
    assert [(item.path, item.line, item.rule) for item in findings] == [
        ("bad.env", 1, "nonempty_hy3_api_key")
    ]
    assert value not in str(findings)


def test_scanner_allows_empty_example_and_explicit_synthetic_bearer() -> None:
    text = "HY3_API_KEY=\nAuthorization: Bearer synthetic-example-token-not-real-123456\n"
    assert scan_text(text, path="fixture.txt") == []


def test_scanner_finds_exact_configured_secret_without_printing_it() -> None:
    value = "novel-secret-format-1234567890"
    findings = scan_text(
        f"ordinary_text={value}\n",
        path="source.txt",
        exact_secrets=[value],
    )
    assert [(item.path, item.line, item.rule) for item in findings] == [
        ("source.txt", 1, "exact_configured_secret")
    ]
    assert value not in str(findings)
