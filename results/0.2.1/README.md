# Implementation 0.2.1 results

Rubric version remains 1.1. These are newly executed, **preliminary** offline results:

- `local-records.csv` and `local-summary.json`: 60 reports, 20/20 strict tier ordering and 6/6
  adversarial detection. Existing human labels are v1.0; comparisons are descriptive only.
- `boundary-checks.json`: 12 supplementary software regression cases, all passing.

No new Hy3 calls, model stability measurements or human labels were collected for this patch.
Earlier real-model artifacts remain untouched under `../v1.1/` and the parent directory.
See [hardening report](../../reports/hardening_0_2_1.md) for changes and reproduction commands.
