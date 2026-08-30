"""Repository secret scanner that reports locations but never prints matched values."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_ASSIGNMENT = re.compile(r"(?i)\bHY3_API_KEY\s*=\s*([^\s#\"'\\]*)")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_OPENAI_STYLE = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}\b")
_BEARER = re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+([A-Za-z0-9._~+\-/=]{8,})")
_URL_CREDENTIAL = re.compile(r"https?://[^:/\s]+:[^@/\s]+@")
_SAFE_ASSIGNMENTS = {"", "replace_me", "placeholder", "example", "your_key_here"}
_SAFE_BEARER_MARKERS = (
    "synthetic",
    "example",
    "test",
    "abcdef",
    "redacted",
    "not-real",
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    path: str
    line: int
    rule: str


def scan_text(
    text: str,
    *,
    path: str,
    exact_secrets: Iterable[str] = (),
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    exact_values = tuple(value for value in exact_secrets if len(value) >= 8)
    for line_number, line in enumerate(text.splitlines(), start=1):
        assignment = _ASSIGNMENT.search(line)
        if assignment and assignment.group(1).strip().casefold() not in _SAFE_ASSIGNMENTS:
            findings.append(SecretFinding(path, line_number, "nonempty_hy3_api_key"))
        if _PRIVATE_KEY.search(line):
            findings.append(SecretFinding(path, line_number, "private_key_block"))
        if _OPENAI_STYLE.search(line):
            findings.append(SecretFinding(path, line_number, "provider_key_pattern"))
        bearer = _BEARER.search(line)
        if bearer and not any(
            marker in bearer.group(1).casefold() for marker in _SAFE_BEARER_MARKERS
        ):
            findings.append(SecretFinding(path, line_number, "authorization_bearer_value"))
        if _URL_CREDENTIAL.search(line):
            findings.append(SecretFinding(path, line_number, "url_embedded_credentials"))
        if any(value in line for value in exact_values):
            findings.append(SecretFinding(path, line_number, "exact_configured_secret"))
    return findings


def repository_candidates(root: Path) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_repository(root: Path, *, exact_secrets: Iterable[str] = ()) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    exact_values = tuple(exact_secrets)
    for path in repository_candidates(root):
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        findings.extend(
            scan_text(
                text,
                path=path.relative_to(root).as_posix(),
                exact_secrets=exact_values,
            )
        )
    return findings
