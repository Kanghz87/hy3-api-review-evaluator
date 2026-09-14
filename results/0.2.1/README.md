# Implementation 0.2.1 results

Rubric version remains 1.1. These are newly executed, **preliminary** offline results:

- `local-records.csv` and `local-summary.json`: 60 reports, 20/20 strict tier ordering and 6/6
  adversarial detection. Existing human labels are v1.0; comparisons are descriptive only.
- `boundary-checks.json`: 12 supplementary software regression cases, all passing.

The initial offline hardening run collected no new Hy3 calls, stability measurements or human labels.
A subsequent real demo preflight on 2026-09-14 used two Hy3 calls (17,278 tokens); see the
[preflight record](../../reports/demo_preflight_0_2_1.md). Its full output is private and Git-ignored.
This single smoke run does not replace the historical full-dataset or stability experiments.
Earlier real-model artifacts remain untouched under `../v1.1/` and the parent directory.
See [hardening report](../../reports/hardening_0_2_1.md) for changes and reproduction commands.
