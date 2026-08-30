"""Fail if a tracked or unignored repository file resembles a real credential."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from hy3_api_review_evaluator.security_scan import scan_repository

ROOT = Path(__file__).parents[1]


def main() -> None:
    ignore = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False)
    if ignore.returncode != 0:
        raise SystemExit("Security failure: .env is not ignored by Git")
    dotenv_key = dotenv_values(ROOT / ".env").get("HY3_API_KEY")
    environment_key = os.getenv("HY3_API_KEY")
    exact_secrets = {
        value
        for value in (dotenv_key, environment_key)
        if isinstance(value, str) and len(value) >= 8
    }
    findings = scan_repository(ROOT, exact_secrets=exact_secrets)
    result = {
        "ok": not findings,
        "scanned_scope": "tracked_and_unignored_files",
        "finding_count": len(findings),
        "findings": [
            {"path": item.path, "line": item.line, "rule": item.rule} for item in findings
        ],
        "matched_values_included": False,
        "exact_local_key_checked": bool(exact_secrets),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
