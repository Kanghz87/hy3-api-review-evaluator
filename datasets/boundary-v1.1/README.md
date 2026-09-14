# v1.1 supplementary boundary corpus

Twelve self-constructed synthetic OpenAPI 3.1 documents, published under the repository license.
This corpus supplements, and never replaces, the frozen 20-scenario / 60-report v1.0 benchmark.

Each manifest entry specifies its source, construction method, difficulty, adversarial status,
expected findings and `human_scores: null`. Expected category, severity, title and source pointer
are declared independently of `audit_spec`; the checker does not generate its own ground truth.
The credential markers are deliberately fake, not working credentials.

Coverage includes:

- four zero-expected-finding documents: ordinary contract, valid `allOf`, operation-level parameter
  override, and sensitive examples;
- parameter reference, chained reference and Path Item reference provenance;
- anonymous `{}` and authenticated-or-anonymous security alternatives;
- valid-but-undocumented required properties versus impossible required/closed properties;
- two distinct response findings sharing the same category and source node.

Run `python evaluation/run_boundary_checks.py` from the repository root. Results are written to
`results/0.2.1/boundary-checks.json` for the current implementation; no API key, model calls or human scores are needed.
Additional tests in `tests/test_boundary_v11.py` exercise empty-report coverage forgery, model
context truncation, negated versus affirmative dangerous suggestions, and deeply nested input.

Two selected documents can also be sent to the real Hy3 reviewer and judge with
`python scripts/run_hy3_smoke.py --boundary clean-contract --run-token-budget 60000` and the
same command with `--boundary parameter-ref`. These are paid, cached integration checks, not
independent generalization estimates. Their model-generated reports remain in versioned results.

No human annotation has been collected for these cases. Passing software assertions is not a new
human agreement or LLM stability result. Cases were added after observing implementation defects,
so they are regression examples, not a prospective held-out benchmark.
