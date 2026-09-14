from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import _result_key
from hy3_api_review_evaluator.models import Focus


def test_result_cache_key_changes_with_focus_and_document() -> None:
    security = _result_key("sha-a", Focus.SECURITY)
    assert security != _result_key("sha-a", Focus.DESIGN)
    assert security != _result_key("sha-b", Focus.SECURITY)
    assert security == _result_key("sha-a", Focus.SECURITY)


def _render_saved_boundary(case_id: str, root_path: str) -> None:
    """Render a saved integration artifact, without creating a provider client."""
    import json
    from pathlib import Path

    from app import _render_results
    from hy3_api_review_evaluator.budget import TokenBudgetLedger
    from hy3_api_review_evaluator.config import Settings
    from hy3_api_review_evaluator.models import EvaluationResult, ReviewReport
    from hy3_api_review_evaluator.spec_loader import load_spec_bytes

    root = Path(root_path)
    payload = json.loads((root / f"results/v1.1/{case_id}-smoke.json").read_text(encoding="utf-8"))
    settings = Settings.from_env(env_file=root / "__missing__.env")
    path = root / f"datasets/boundary-v1.1/{case_id}.json"
    spec = load_spec_bytes(path.read_bytes(), path.name, settings)
    _render_results(
        spec,
        ReviewReport.model_validate(payload["review"]),
        EvaluationResult.model_validate(payload["evaluation"]),
        TokenBudgetLedger(None, total_limit=850_000, run_limit=60_000),
    )


@pytest.mark.parametrize("case_id", ["clean-contract", "parameter-ref"])
def test_saved_boundary_results_render_without_model_calls(case_id: str) -> None:
    app = AppTest.from_function(
        _render_saved_boundary, args=(case_id, str(Path(__file__).parents[1]))
    ).run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "通过"
    if case_id == "clean-contract":
        assert any(expander.label == "审查范围证据" for expander in app.expander)


def _render_short_secret_result(document_json: str, report_json: str, evaluation_json: str) -> None:
    from pathlib import Path

    from app import _render_results
    from hy3_api_review_evaluator.budget import TokenBudgetLedger
    from hy3_api_review_evaluator.config import Settings
    from hy3_api_review_evaluator.models import EvaluationResult, ReviewReport
    from hy3_api_review_evaluator.spec_loader import load_spec_text

    settings = Settings.from_env(env_file=Path("__missing__.env"))
    _render_results(
        load_spec_text(document_json, "probe.json", settings),
        ReviewReport.model_validate_json(report_json),
        EvaluationResult.model_validate_json(evaluation_json),
        TokenBudgetLedger(None, total_limit=850_000, run_limit=60_000),
    )


@pytest.mark.parametrize("secret", ["all", "pass", "low", "hy3", "a"])
def test_short_secret_results_render_without_model_calls(secret, settings):
    import json

    from test_redaction_boundaries import make_result

    spec, report, evaluation = make_result(secret, settings)
    evaluation = evaluation.model_copy(update={"verdict": "pass"})
    app = AppTest.from_function(
        _render_short_secret_result,
        args=(json.dumps(spec.document), report.model_dump_json(), evaluation.model_dump_json()),
    ).run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "通过"
    assert any("[REDACTED]" in item.value for item in app.markdown)
