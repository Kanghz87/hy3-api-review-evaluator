from __future__ import annotations

import json

from hy3_api_review_evaluator.cli import main


def test_cli_check_never_prints_key(monkeypatch, capsys) -> None:
    monkeypatch.setenv("HY3_API_KEY", "unit-test-secret-never-print")
    monkeypatch.setattr("sys.argv", ["hy3-evaluate", "check"])

    assert main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["settings"]["api_key_present"] is True
    assert "unit-test-secret-never-print" not in output


def test_cli_local_audit(monkeypatch, capsys) -> None:
    spec = "datasets/specs/easy-01-missing-description.yaml"
    monkeypatch.setattr("sys.argv", ["hy3-evaluate", "audit-local", str(spec)])

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["document"]["openapi_version"] == "3.1.0"
    assert payload["document"]["operation_count"] == 1
    assert payload["finding_count"] >= 1
