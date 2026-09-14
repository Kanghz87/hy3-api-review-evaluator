"""Check independently declared v1.1 contract boundaries, without calling any model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hy3_api_review_evaluator import __version__
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evidence import check_evidence
from hy3_api_review_evaluator.redaction import redact_structure
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]


def run() -> dict:
    folder = ROOT / "datasets" / "boundary-v1.1"
    cases = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    settings = Settings.from_env(env_file=ROOT / "__missing__.env")
    records = []
    for case in cases:
        path = folder / f"{case['id']}.json"
        spec = load_spec_bytes(path.read_bytes(), path.name, settings)
        findings = audit_spec(spec)
        actual = [
            {
                "category": f.category,
                "location": f.location,
                "severity": f.severity,
                "title": f.title,
            }
            for f in findings
        ]
        evidence_valid = all(
            check_evidence(spec.document, e).quote_matches for f in findings for e in f.evidence
        )
        sanitized = json.dumps(redact_structure(spec.document))
        markers_removed = all(m not in sanitized for m in case.get("sensitive_markers", []))
        records.append(
            {
                "case_id": case["id"],
                "spec_sha256": spec.sha256,
                "difficulty": case["difficulty"],
                "expected_findings": case["expected_findings"],
                "actual_findings": actual,
                "evidence_valid": evidence_valid,
                "sensitive_markers_removed": markers_removed,
                "passed": (
                    sorted(actual, key=str) == sorted(case["expected_findings"], key=str)
                    and evidence_valid
                    and markers_removed
                ),
            }
        )
    return {
        "evaluation_version": "1.1",
        "implementation_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if all(r["passed"] for r in records) else "failed",
        "case_count": len(records),
        "passed_count": sum(r["passed"] for r in records),
        "zero_expected_finding_case_count": sum(not c["expected_findings"] for c in cases),
        "new_hy3_calls": 0,
        "human_scores": None,
        "scope": "Software regression only; not held-out model performance or human agreement.",
        "records": records,
    }


if __name__ == "__main__":
    result = run()
    output = ROOT / "results" / __version__ / "boundary-checks.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
