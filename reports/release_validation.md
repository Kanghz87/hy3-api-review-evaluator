# Local delivery validation — 2026-08-27

Scope: final local engineering checks before synchronizing the human-annotation update to the
existing **private** repository. This is not a public release or an activity submission.

## Actual checks

| Check | Observed result |
| --- | --- |
| Python / platform | Python 3.12.10, Windows |
| Ruff lint and formatting | Passed; 63 Python files formatted |
| Unit and integration tests | 54 passed |
| Dataset validation | 20 scenarios, 60 reports, frozen human subset 33/33 |
| Deterministic ordering rerun | 20/20 scenarios strictly good > medium > bad |
| Saved real-result validation | 60 hybrid records and 18 stability evaluations valid |
| Human agreement reproduction | N=33; Spearman 0.9480394266171248; MAE 4.166666666666667 |
| Isolated build | sdist and wheel version 0.1.0 built successfully |
| Fresh wheel installation | New venv without system packages; `pip check` passed |
| Installed package origin | Import resolved inside the new venv, not the editable source tree |
| CLI and bundled Rubric | CLI loaded; six Rubric dimensions loaded |
| Streamlit applications | Main and annotation pages loaded through `AppTest` without exceptions |
| Fresh-environment data checks | Dataset, saved model results and human agreement validated |
| Credential checks | Repository pattern scan and exact local-key check: zero findings |
| Distribution checks | Both archives scanned; no local key, `.env`, raw local annotations or private ledger |
| Source distribution contents | Both apps, human-metric code, canonical annotations and results present |
| Additional Hy3 calls / tokens | 0 / 0; cumulative experimental usage remains 211,101 tokens |

The fresh environment resolved OpenAI SDK 2.54.0, pandas 3.0.5, Pydantic 2.13.4, PyYAML 6.0.3,
python-dotenv 1.2.3 and Streamlit 1.62.0. These are observed versions, not a dependency lockfile.
The OpenAI SDK is only the compatibility client; no OpenAI model was called.

The local default package mirror could not supply the build dependency. Only the verification
process used `PIP_INDEX_URL=https://pypi.org/simple`; no global pip configuration was changed.

## Reproduction and integrity

Use the commands under **Tests and build** in the README. `scripts/verify_clean_install.py` creates
`tmp/clean-install-venv`, installs the built wheel, verifies imports and both repository app files,
then checks saved results without a provider call. It removes only its temporary venv afterward.
`--keep` retains that environment for diagnosis.

The original local human CSV, frozen selection protocol, raw hybrid output and raw stability output
retained their pre-validation SHA-256 values. No human scores, model scores, Rubric thresholds or
selection choices were adjusted. Exact-key comparison kept credential values in memory and emitted
only counts and file/rule locations; no credential value was written into the report.

The CI workflow repeats checks, build and fresh installation on Python 3.11 and 3.12. Its actual
remote outcome is available in the repository's **Actions** tab; this local record does not stand
in for a successful remote run.

## Remaining delivery work

Record and inspect the two-minute Demo; obtain separate approval before changing repository
visibility or submitting activity materials. The page-loading smoke test is not a recorded Demo
and does not re-run live Hy3 calls. Prior live-call evidence remains in the experiment results.
