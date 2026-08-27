# Security model

## Trust boundaries

The API key and local process environment are trusted configuration. Uploaded OpenAPI text,
descriptions, examples, extensions, `$ref` values, model output, evaluated reports, evidence quotes,
CSV cells, and provider error bodies are untrusted.

No generated or uploaded content is interpreted as a command, imported as Python, rendered as raw
HTML, or executed as a contract test.

## Threats and controls

| Threat | Control | Residual risk |
| --- | --- | --- |
| API key committed to Git | `.env` and secrets files ignored; Key read only from environment; repository scanner | A novel key format may evade generic patterns; exact pre-release review is still required |
| Oversized or recursive YAML | Byte, node, and depth limits; safe loader; YAML alias rejection | A valid document near every limit can still be computationally expensive |
| Remote `$ref` exfiltration or SSRF | Only bounded local `#/` pointers resolve; external refs are recorded but never fetched | Review completeness is lower until the user bundles references locally |
| Prompt injection in descriptions or examples | Explicit untrusted tags and system instructions; local findings and pointers remain authoritative | No prompt defense is perfect; output must still pass evidence and schema gates |
| Sensitive values sent to Hy3 | Structural and regex redaction before projection and deterministic-evidence prompts | Unknown secret formats may remain; users must not upload production credentials |
| Model invents endpoint or quote | Strict Pydantic output; pointer existence; exact quote match; high/critical failures force fail | A quote can exist while the model's semantic interpretation is still wrong |
| Judge is manipulated by report length or jargon | Length and terminology never add score; duplicates are ignored; hard local ceilings | The semantic judge may still vary within the allowed ceiling |
| Provider error leaks Authorization | Provider body is not exposed; errors map to status categories; exact Key scrubbed again in UI | Lower-level platform logging is outside this repository's control |
| CSV formula injection | Cells beginning with `=`, `+`, `-`, `@`, tab, or CR are prefixed with an apostrophe | Spreadsheet software may have vendor-specific behavior |
| Generated content is executed | Application only displays or downloads content; no shell, eval, import, or HTTP execution path | A user may manually execute downloaded advice outside the application |
| Unexpected API spend | Persistent token ledger, conservative reservation, per-run cap, 850,000 total cap | Provider billing rules can differ from reported usage; console billing should also be monitored |

## Input limits

Defaults are deliberately bounded:

- 2,000,000 bytes per uploaded file
- 200,000 container nodes
- nesting depth 100
- local `$ref` chain depth 16
- 120,000 model projection characters
- 100 findings per accepted final report
- five evidence references per finding

Every limit is validated before the corresponding expensive or recursive operation.

## Key handling

The application accepts `HY3_API_KEY` only from the process environment or local `.env`. The
`Settings.safe_summary()` method returns `api_key_present`, never the value. Token ledgers contain
only timestamps, purpose labels, and usage counts.

Before every commit or release:

```powershell
.venv\Scripts\python.exe scripts\scan_secrets.py
git status --short
git diff --cached
```

The scanner examines tracked and unignored files and reports only file, line number, and rule name.
It never prints the matched credential-like value.

## Output handling

All model responses must parse as one JSON object and pass a strict Pydantic schema with unknown
fields rejected. Invalid JSON, missing dimensions, duplicate judge dimensions, invalid severity,
invalid score, or model substitution causes a safe failure; content is not partially accepted.

Evidence previews are redacted before UI or export. The original uploaded document is not included
in JSON exports; only its label, hash, version, operation count, and unresolved external-ref list are
included.

## Not a sandbox or runtime verifier

This project does not call user APIs, execute examples, validate server implementations, download
schemas, or prove that suggested changes are safe in a particular production system. Run any manual
changes through normal code review, OpenAPI validation, tests, and deployment controls.

