from __future__ import annotations

from app import _result_key
from hy3_api_review_evaluator.models import Focus


def test_result_cache_key_changes_with_focus_and_document() -> None:
    security = _result_key("sha-a", Focus.SECURITY)
    assert security != _result_key("sha-a", Focus.DESIGN)
    assert security != _result_key("sha-b", Focus.SECURITY)
    assert security == _result_key("sha-a", Focus.SECURITY)
