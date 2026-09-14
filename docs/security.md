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
| Oversized or recursive YAML | Streamlit upload cap plus byte, node, and depth limits; safe loader; YAML alias rejection | A valid document near every limit can still be computationally expensive |
| Remote `$ref` exfiltration or SSRF | Only bounded local `#/` pointers resolve; external refs are recorded but never fetched | Review completeness is lower until the user bundles references locally |
| Prompt injection in descriptions or examples | Explicit untrusted tags and system instructions; local findings and pointers remain authoritative | No prompt defense is perfect; output must still pass evidence and schema gates |
| Sensitive values sent to Hy3 | Structural and regex redaction before projection and deterministic-evidence prompts | Unknown secret formats may remain; users must not upload production credentials |
| Model invents endpoint or quote | Strict Pydantic output; pointer existence; exact quote match; semantic Hy3 judge; high/critical failures force fail | A real quote can still be attached to an unrelated claim; the semantic judge is required for this case |
| Judge is manipulated by report length or jargon | Length and terminology never add score; duplicates are ignored; hard local ceilings | The semantic judge may still vary within the allowed ceiling |
| Provider error leaks Authorization | Provider body is not exposed; errors map to status categories; exact Key scrubbed again in UI | Lower-level platform logging is outside this repository's control |
| CSV formula injection | Cells beginning with `=`, `+`, `-`, `@`, tab, or CR are prefixed with an apostrophe | Spreadsheet software may have vendor-specific behavior |
| Generated content is executed | Application only displays or downloads content; no shell, eval, import, or HTTP execution path | A user may manually execute downloaded advice outside the application |
| Unexpected API spend | Cross-process ledger lock, persistent reservations, conservative unknown-usage charge, per-run cap, 850,000 total cap | A crash can leave a fail-closed reservation; provider billing can differ from reported usage, so console billing should also be monitored |

## Input limits

Defaults are deliberately bounded:

- 2,000,000 bytes per uploaded file
- 200,000 container nodes
- nesting depth 100
- local `$ref` chain depth 16
- 120,000 model projection characters
- 100 findings per accepted final report
- five evidence references per finding

YAML construction counts depth and nodes before descending; aliases are rejected. JSON parsing
recursion failures are converted to safe input errors, and both formats receive a post-parse shape
check. These bounds reduce resource exhaustion risk but are not a general-purpose process sandbox.

Sensitive schema context propagates to `example`, `examples`, `default`, `enum` and `const`, including
parameters whose sensitive name is stored separately and password-format schemas. This preserves
schema types while masking values. Evidence previews use the redacted document, not an isolated
leaf that has lost its field context.

Implementation 0.2.1 also propagates sensitive context through local references, including named
component examples, and recognizes `X-API-Key` and header names declared by API-key schemes.
Identified values are scrubbed from copied report text before judge requests, UI rendering and JSON
export. CSV integrations must pass `spec=loaded_spec` to `build_csv_export` for document-aware
scrubbing; the application does so. The legacy two-argument helper only performs text-pattern
redaction because it has no source document. No schema references are downloaded.

JSON duplicate keys and duplicate normalized YAML keys are rejected. Unquoted YAML timestamps are
kept as strings; integer mapping keys normalize to strings. Non-JSON scalar types and non-finite
numbers are rejected, and source pointers are limited to 500 characters. Inputs producing more
than 100 deterministic findings or oversized deterministic context fail before a model request.
Over-capacity model output is rejected without silently dropping findings. Contradictory judge
failure flags/reasons are rejected instead of being interpreted as a passing result.

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

Document-aware output redaction preserves typed protocol enums (such as focus, severity and
verdict), evaluator-generated hashes and implementation metadata. This exemption does not apply
to arbitrary input dictionaries or narrative text. Sensitive finding IDs are replaced with stable
opaque IDs in both reports and assessments. Text can be truncated to its schema limit after
redaction; pointers containing sensitive text may also be masked. These sanitized output copies
are for presentation/export, not rescoring; factual checks always use the original in-memory
report and document. A missing location in the redacted projection is never treated as JSON null
or as evidence for a redaction placeholder.

Empty reports must provide matching coverage evidence, cannot hide locally known issues, and cannot
pass with truncated or externally incomplete context. Even complete local coverage is conditional
until a semantic judge checks the no-findings claim. This is report assessment, not API certification.

The unsafe-advice heuristic examines authored recommendations and recognizes immediate negation;
it does not treat malicious quoted evidence as an endorsed action. Indirect language, complex
negation, novel secret formats and semantic attacks still require model judgment and human review.

## Not a sandbox or runtime verifier

This project does not call user APIs, execute examples, validate server implementations, download
schemas, or prove that suggested changes are safe in a particular production system. Run any manual
changes through normal code review, OpenAPI validation, tests, and deployment controls.
