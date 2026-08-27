from __future__ import annotations

import pytest

from hy3_api_review_evaluator.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="unit-test-placeholder",
        base_url="https://tokenhub.tencentmaas.com/v1",
        model="hy3",
        timeout_seconds=10,
        max_retries=0,
        reasoning_effort="high",
        max_file_bytes=200_000,
        max_container_nodes=20_000,
        max_nesting_depth=50,
        max_model_chars=30_000,
        max_output_tokens=2_000,
        total_token_budget=850_000,
        default_run_token_budget=20_000,
    )
