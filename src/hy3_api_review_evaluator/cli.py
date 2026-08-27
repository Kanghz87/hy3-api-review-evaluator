"""Small command-line entry point for configuration checks and local OpenAPI audits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import Settings
from .redaction import redact_structure
from .rules.audit import audit_spec
from .spec_loader import load_spec_bytes


def _settings() -> Settings:
    return Settings.from_env(env_file=Path.cwd() / ".env")


def _print_json(value: Any) -> None:
    print(json.dumps(redact_structure(value), ensure_ascii=False, indent=2))


def _check(_: argparse.Namespace) -> int:
    _print_json({"ok": True, "settings": _settings().safe_summary()})
    return 0


def _audit(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    settings = _settings()
    loaded = load_spec_bytes(path.read_bytes(), path.name, settings)
    findings = audit_spec(loaded)
    _print_json(
        {
            "document": {
                "filename": path.name,
                "sha256": loaded.sha256,
                "openapi_version": loaded.version,
                "operation_count": loaded.operation_count,
                "external_ref_count": len(loaded.external_refs),
            },
            "finding_count": len(findings),
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hy3-evaluate", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="Show secret-free runtime configuration")
    check.set_defaults(handler=_check)
    audit = subparsers.add_parser("audit-local", help="Run deterministic checks without Hy3")
    audit.add_argument("file", help="Path to a UTF-8 OpenAPI 3.x JSON/YAML document")
    audit.set_defaults(handler=_audit)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
