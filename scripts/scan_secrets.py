"""Fail if a tracked or unignored repository file resembles a real credential."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hy3_api_review_evaluator.security_scan import scan_repository

ROOT = Path(__file__).parents[1]


def main() -> None:
    ignore = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False)
    if ignore.returncode != 0:
        raise SystemExit("Security failure: .env is not ignored by Git")
    findings = scan_repository(ROOT)
    result = {
        "ok": not findings,
        "scanned_scope": "tracked_and_unignored_files",
        "finding_count": len(findings),
        "findings": [
            {"path": item.path, "line": item.line, "rule": item.rule} for item in findings
        ],
        "matched_values_included": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
