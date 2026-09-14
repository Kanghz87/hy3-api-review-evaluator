# Evaluation v1.1 results

These files are isolated from frozen v1.0 artifacts in the parent folder.

- `local-records.csv` / `local-summary.json`: executed deterministic reevaluation of 60 original
  reports. Comparisons against v1.0 human labels are descriptive, not newly collected human scores.
- `boundary-checks.json`: twelve independently declared software regression cases, no model calls.
- `clean-contract-smoke.json` / `parameter-ref-smoke.json`: real Hy3 review + judge runs completed
  on 2026-09-11. Reading these files or replaying them in UI tests performs no new API calls.

No full v1.1 hybrid or repeat-stability run has been collected. Do not copy historical outputs into
this directory as if they were new measurements. Research status remains **preliminary**.

See [revision analysis](../../reports/revision_v1_1.md) for actual measurements, changed scores,
budget accounting, limitations, and reproduction commands.
