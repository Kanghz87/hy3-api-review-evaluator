from __future__ import annotations

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evidence import check_evidence
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import load_spec_text

INSECURE = """
openapi: 3.1.0
info: {title: Demo, version: 1.0.0}
servers: [{url: http://api.example.test}]
paths:
  /users/{id}:
    delete:
      operationId: duplicate
      parameters:
        - {name: id, in: path, required: false, schema: {type: string}}
      responses: {'204': {description: deleted}}
  /users/{missing}:
    get:
      operationId: duplicate
      responses: {}
components:
  securitySchemes:
    bearerAuth: {type: http, scheme: bearer}
  schemas:
    User:
      type: object
      required: [id]
      properties: {name: {type: string}}
"""


def test_rules_find_grounded_high_value_issues(settings: Settings) -> None:
    spec = load_spec_text(INSECURE, "insecure.yaml", settings)
    findings = audit_spec(spec)
    categories = {finding.category for finding in findings}
    assert {
        "transport_security",
        "operation_id",
        "path_parameter",
        "authorization",
        "schema",
    } <= categories
    assert all(
        check_evidence(spec.document, evidence).exists
        for finding in findings
        for evidence in finding.evidence
    )


def test_external_refs_produce_explicit_incompleteness_finding(settings: Settings) -> None:
    spec = load_spec_text(
        """
openapi: 3.0.3
info: {title: External, version: 1.0.0, description: demo}
paths: {}
components:
  schemas:
    Remote: {$ref: 'file:///private/schema.yaml'}
""",
        "external.yaml",
        settings,
    )
    findings = audit_spec(spec)
    assert any(finding.category == "external_reference" for finding in findings)


def test_deterministic_evidence_redacts_secret_and_still_matches(settings: Settings) -> None:
    secret = "abcdefghijklmnop"
    spec = load_spec_text(
        INSECURE.replace(
            "operationId: duplicate",
            f"description: 'Bearer {secret}'\n      operationId: duplicate",
            1,
        ),
        "secret.yaml",
        settings,
    )
    findings = audit_spec(spec)
    serialized = " ".join(evidence.quote for finding in findings for evidence in finding.evidence)
    assert secret not in serialized
    assert all(
        check_evidence(spec.document, evidence).quote_matches
        for finding in findings
        for evidence in finding.evidence
    )
